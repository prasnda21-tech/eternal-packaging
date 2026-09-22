/** Installed from the main bundle so it runs before the router popstate listener. */
let guard = null;

export function setProductRequestLeaveGuard(next) {
  guard = typeof next === "function" ? next : null;
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", (event) => {
    if (guard && guard(event)) event.stopImmediatePropagation();
  }, true);
}

/** Browser refresh/close prompt. Modern browsers ignore a custom message. */
export function armProductRequestBeforeUnload(event) {
  event.preventDefault();
  event.returnValue = "";
}
