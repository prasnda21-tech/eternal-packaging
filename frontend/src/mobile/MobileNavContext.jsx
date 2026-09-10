import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const MobileNavContext = createContext({
  menuOpen: false,
  openMenu: () => {},
  closeMenu: () => {},
  toggleMenu: () => {},
});

export function MobileNavProvider({ children }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const openMenu = useCallback(() => setMenuOpen(true), []);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const toggleMenu = useCallback(() => setMenuOpen((v) => !v), []);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const value = useMemo(
    () => ({ menuOpen, openMenu, closeMenu, toggleMenu }),
    [menuOpen, openMenu, closeMenu, toggleMenu],
  );

  return <MobileNavContext.Provider value={value}>{children}</MobileNavContext.Provider>;
}

export function useMobileNav() {
  return useContext(MobileNavContext);
}
