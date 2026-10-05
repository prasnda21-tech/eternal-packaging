import { useMemo } from "react";
import { useAuth } from "../lib/auth";
import {
  clearGlobalWorkspaceTabs,
  createGlobalWorkspaceApi,
} from "../lib/globalWorkspace";
import { useWorkspaceTabs } from "./useWorkspaceTabs";

export function useGlobalWorkspaceTabs() {
  const { user } = useAuth();
  const permKey = (user?.permissions || []).join("\0");
  const api = useMemo(
    () => createGlobalWorkspaceApi((key) => (user?.permissions || []).includes(key), user),
    // permKey and role capture identity without depending on a new hasPerm fn each render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [user?.id, user?.email, user?.sub, user?.role, permKey],
  );

  const workspace = useWorkspaceTabs({
    load: api.load,
    save: api.save,
    openOrActivate: api.openOrActivate,
    closeTab: api.closeTab,
  });

  return {
    ...workspace,
    tabByPath: api.tabByPath,
  };
}

export { clearGlobalWorkspaceTabs };
