import { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Exclusive accordion for Detail PO stage cards.
 * Only one stage body is expanded at a time; children stay mounted (hidden)
 * so unsaved inputs are preserved when another card opens.
 *
 * May be controlled by the stage-focused workspace (`openStageIndex` + `onToggleStage`).
 */
const PoStageAccordionContext = createContext(null);

export function PoStageAccordionProvider({
  children,
  openStageIndex: controlledIndex,
  onToggleStage,
}) {
  const [uncontrolled, setUncontrolled] = useState(null);
  const isControlled = controlledIndex !== undefined;
  const openStageIndex = isControlled ? controlledIndex : uncontrolled;

  const toggleStage = useCallback((stageIndex) => {
    if (typeof onToggleStage === "function") {
      onToggleStage(stageIndex);
      return;
    }
    setUncontrolled((prev) => (prev === stageIndex ? null : stageIndex));
  }, [onToggleStage]);

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
