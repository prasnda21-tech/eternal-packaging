import { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Exclusive accordion for Detail PO stage cards.
 * Only one stage body is expanded at a time; children stay mounted (hidden)
 * so unsaved inputs are preserved when another card opens.
 */
const PoStageAccordionContext = createContext(null);

export function PoStageAccordionProvider({ children }) {
  const [openStageIndex, setOpenStageIndex] = useState(null);

  const toggleStage = useCallback((stageIndex) => {
    setOpenStageIndex((prev) => (prev === stageIndex ? null : stageIndex));
  }, []);

  const value = useMemo(
    () => ({ openStageIndex, toggleStage }),
    [openStageIndex, toggleStage]
  );

  return (
    <PoStageAccordionContext.Provider value={value}>
      {children}
    </PoStageAccordionContext.Provider>
  );
}

export function usePoStageAccordion(stageIndex) {
  const ctx = useContext(PoStageAccordionContext);
  if (!ctx) {
    throw new Error("usePoStageAccordion requires PoStageAccordionProvider");
  }
  return {
    open: ctx.openStageIndex === stageIndex,
    toggle: () => ctx.toggleStage(stageIndex),
  };
}
