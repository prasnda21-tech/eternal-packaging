/**
 * Shared helpers for full-row document navigation on Sales list tables.
 * Nested interactive controls must not trigger row open.
 */

export const ROW_INTERACTIVE_SELECTOR = [
  "a",
  "button",
  "input",
  "select",
  "textarea",
  "label",
  "option",
  "[role='button']",
  "[role='menuitem']",
  "[role='checkbox']",
  "[role='combobox']",
  "[role='listbox']",
  "[data-no-row-nav]",
].join(",");

export function isRowInteractiveTarget(target, currentTarget) {
  if (!(target instanceof Element) || !(currentTarget instanceof Element)) {
    return false;
  }
  const interactive = target.closest(ROW_INTERACTIVE_SELECTOR);
  return Boolean(interactive && currentTarget.contains(interactive));
}

/**
 * Props for a navigable <tr>. Caller supplies className merge and open handler.
 */
export function clickableDocumentRowProps({
  onOpen,
  disabled = false,
  className = "",
  "data-testid": testId,
  ariaLabel,
}) {
  const classes = ["sd-clickable-row", className].filter(Boolean).join(" ");
  if (disabled || typeof onOpen !== "function") {
    return {
      className: className || undefined,
      "data-testid": testId,
    };
  }
  return {
    role: "link",
    tabIndex: 0,
    className: classes,
    "data-testid": testId,
    "aria-label": ariaLabel,
    onClick: (event) => {
      if (isRowInteractiveTarget(event.target, event.currentTarget)) return;
      onOpen(event);
    },
    onKeyDown: (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target !== event.currentTarget) return;
      event.preventDefault();
      onOpen(event);
    },
  };
}
