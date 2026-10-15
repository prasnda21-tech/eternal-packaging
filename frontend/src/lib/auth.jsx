import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api from "./api";
import { loginThenLoadUser } from "./authSession";
import { clearCashBankWorkspaceTabs } from "./cashBankWorkspace";
import { clearGlobalWorkspaceTabs } from "./globalWorkspace";
import { clearMobileOpsSessionForUser } from "./mobileOpsSession";
import { clearProductionSummaryCache } from "./productionSummaryCache";
import { clearPoListReturnForUser, storageUserKey } from "./poListReturn";
import { clearSalesPwaPoListState } from "./salesPwaPoListState";
import { clearSalesInvoiceListCache } from "./salesInvoiceListCache";
import { clearSalesOrderListCache } from "./salesOrderListCache";
import { clearSalesWorkspaceTabs } from "./salesWorkspace";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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
      clearSalesInvoiceListCache();
      clearSalesOrderListCache();
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
