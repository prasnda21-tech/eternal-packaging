import React, { createContext, useContext, useState, useMemo, useCallback } from "react";

const FormStandardContext = createContext(null);

export function calculateProgress(fields = {}) {
  const allRequired = Object.values(fields).filter((f) => f.isRequired);
  if (allRequired.length === 0) {
    return { percentage: 100, completed: 0, total: 0 };
  }
  const completed = allRequired.filter((f) => f.value !== undefined && f.value !== null && f.value !== "").length;
  const total = allRequired.length;
  return {
    percentage: Math.round((completed / total) * 100),
    completed,
    total,
  };
}

export function computeSectionStatus(sectionFields = []) {
  if (!sectionFields || sectionFields.length === 0) return "neutral";
  const hasError = sectionFields.some((f) => !!f.error);
  if (hasError) return "error";

  const requiredFields = sectionFields.filter((f) => f.isRequired);
  if (requiredFields.length === 0) return "complete";

  const completedRequired = requiredFields.filter((f) => f.value !== undefined && f.value !== null && f.value !== "");
  if (completedRequired.length < requiredFields.length) {
    return "incomplete";
  }
  return "complete";
}

export function FormStandardProvider({ children, defaultValues = {} }) {
  const [fields, setFields] = useState({});

  const registerField = useCallback((id, config = {}) => {
    setFields((prev) => {
      if (prev[id]) return prev;
      return {
        ...prev,
        [id]: {
          id,
          value: defaultValues[id] !== undefined ? defaultValues[id] : (config.value !== undefined ? config.value : ""),
          isRequired: Boolean(config.isRequired),
          error: config.error || null,
          isTouched: false,
          isFocused: false,
          section: config.section || "default",
        },
      };
    });
  }, [defaultValues]);

  const updateField = useCallback((id, updates = {}) => {
    setFields((prev) => {
      if (!prev[id]) return prev;
      return {
        ...prev,
        [id]: { ...prev[id], ...updates },
      };
    });
  }, []);

  const getSectionStatus = useCallback((sectionId) => {
    const sectionFields = Object.values(fields).filter((f) => f.section === sectionId);
    return computeSectionStatus(sectionFields);
  }, [fields]);

  const progress = useMemo(() => {
    return calculateProgress(fields);
  }, [fields]);

  const scrollToFirstInvalid = useCallback((containerId = null) => {
    const root = containerId ? document.getElementById(containerId) : document;
    if (!root) return false;

    // Search for actual error field first, then incomplete required field
    const target =
      root.querySelector(".fs-error [id]") ||
      root.querySelector("[aria-invalid='true']") ||
      root.querySelector(".fs-incomplete [id]") ||
      root.querySelector("[aria-required='true']:invalid");

    if (target) {
      const prefersReducedMotion =
        typeof window !== "undefined" &&
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      if (typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "center" });
      }
      setTimeout(() => {
        if (typeof target.focus === "function") {
          target.focus({ preventScroll: true });
        }
      }, prefersReducedMotion ? 0 : 250);
      return true;
    }
    return false;
  }, []);

  const value = {
    fields,
    registerField,
    updateField,
    getSectionStatus,
    progress,
    scrollToFirstInvalid,
  };

  return (
    <FormStandardContext.Provider value={value}>
      {children}
    </FormStandardContext.Provider>
  );
}

const fallbackContext = {
  fields: {},
  registerField: () => {},
  updateField: () => {},
  getSectionStatus: () => "neutral",
  progress: { percentage: 100, completed: 0, total: 0 },
  scrollToFirstInvalid: (containerId = null) => {
    const root = containerId ? document.getElementById(containerId) : document;
    if (!root) return false;
    const target =
      root.querySelector(".fs-error [id]") ||
      root.querySelector("[aria-invalid='true']") ||
      root.querySelector(".fs-incomplete [id]");
    if (target) {
      target.scrollIntoView?.({ behavior: "smooth", block: "center" });
      target.focus?.({ preventScroll: true });
      return true;
    }
    return false;
  },
};

export function useFormStandard() {
  const context = useContext(FormStandardContext);
  return context || fallbackContext;
}
