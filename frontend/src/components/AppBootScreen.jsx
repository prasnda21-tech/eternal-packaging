import { Navigate, useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { loginPathForLocation, postLoginPath } from "../lib/mobileAuthRedirect";

/** Neutral boot frame. No nav, identity, role-based controls, or business data. */
export default function AppBootScreen() {
  return (
    <div
      className="eternal-boot"
      data-testid="app-boot-screen"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <p className="eternal-boot-kicker">ETERNAL PACKAGING</p>
      <p className="eternal-boot-msg">Memuat...</p>
    </div>
  );
}

export function Protected({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <AppBootScreen />;
  if (!user) return <Navigate to={loginPathForLocation(location.pathname, location.search)} replace />;
  return children;
}

export function PublicOnly({ children }) {
  const { user, loading } = useAuth();
  const [params] = useSearchParams();
  if (loading) return <AppBootScreen />;
  if (user) return <Navigate to={postLoginPath(params.get("next"))} replace />;
  return children;
}
