import { useEffect, useState } from "react";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { installAffordance, isIosUserAgent, isStandaloneDisplay } from "../lib/mobilePwa";
import MobileTopBar from "./MobileTopBar";

export default function MobileProfilePage() {
  const { user, logout } = useAuth();
  const [promptEvent, setPromptEvent] = useState(null);
  const [hint, setHint] = useState("");
  const standalone = isStandaloneDisplay();
  const ios = isIosUserAgent(typeof navigator !== "undefined" ? navigator.userAgent : "");
  const affordance = installAffordance({ deferredPrompt: promptEvent, isStandalone: standalone, isIos: ios });
  const initial = String(user?.name || user?.email || "?").trim().charAt(0).toUpperCase();

  useEffect(() => {
    const onPrompt = (e) => {
      e.preventDefault();
      setPromptEvent(e);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  const install = async () => {
    if (!promptEvent) return;
    promptEvent.prompt();
    await promptEvent.userChoice;
    setPromptEvent(null);
  };

  return (
    <>
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Profil" />
      <main className="mop-content" data-testid="mobile-profile-page">
        <div className="mop-profile-hero">
          <div className="mop-avatar" aria-hidden>{initial}</div>
          <div>
            <strong>{user?.name || "Pengguna"}</strong>
            <div className="mop-card-meta">{ROLES[user?.role] || user?.role || ""}</div>
            {user?.email ? <div className="mop-card-meta">{user.email}</div> : null}
          </div>
        </div>
        <button type="button" className="mop-list-btn" onClick={() => setHint("Pengaturan akun tetap di aplikasi desktop.")}>
          Pengaturan
        </button>
        {hint && <p className="mop-card-meta">{hint}</p>}
        <button type="button" className="mop-list-btn" onClick={logout} data-testid="mobile-logout">
          Keluar
        </button>
        {affordance.show && (
          <div className="mop-file" data-testid="mobile-install">
            <strong>Eternal Packaging PWA</strong>
            <p>Install Eternal Packaging</p>
            <p className="mop-card-meta">Akses lebih cepat dari layar utama.</p>
            {affordance.kind === "prompt" && (
              <button type="button" className="mop-btn-primary" onClick={install}>Install</button>
            )}
            {affordance.kind === "ios-hint" && (
              <p className="mop-card-meta">Di iPhone: Share → Add to Home Screen.</p>
            )}
            {affordance.kind === "guidance" && (
              <p className="mop-card-meta">Install tersedia setelah aplikasi dibuka lewat HTTPS.</p>
            )}
          </div>
        )}
      </main>
    </>
  );
}
