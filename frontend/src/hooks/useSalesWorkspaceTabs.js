import { useWorkspaceTabs } from "./useWorkspaceTabs";
import {
  clearSalesWorkspaceTabs,
  closeSalesTab,
  loadSalesWorkspaceTabs,
  openOrActivateSalesTab,
  saveSalesWorkspaceTabs,
} from "../lib/salesWorkspace";

export function useSalesWorkspaceTabs() {
  return useWorkspaceTabs({
    load: loadSalesWorkspaceTabs,
    save: saveSalesWorkspaceTabs,
    openOrActivate: openOrActivateSalesTab,
    closeTab: closeSalesTab,
  });
}

export { clearSalesWorkspaceTabs };
