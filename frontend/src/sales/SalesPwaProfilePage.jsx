import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

export default function SalesPwaProfilePage() {
  const { logout } = useAuth();
  const [profile, setProfile] = useState(null);

  useEffect(() => {
    api.salesPwaMe().then(setProfile).catch(() => setProfile(null));
  }, []);

  const onLogout = async () => {
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
    await logout();
  };

  return (
    <>
      <header className="spwa-top">
        <p className="spwa-kicker">PWA SALES</p>
        <h1>Profil</h1>
      </header>
      <main className="spwa-main" data-testid="sales-pwa-profile">
        <section className="spwa-card">
          <h2>{profile?.name || "Pengguna"}</h2>
          <dl className="spwa-kv">
            <dt>Role</dt><dd>{profile?.role_label || "Sales Eternal"}</dd>
            <dt>Cabang</dt><dd>{profile?.branch || "—"}</dd>
            <dt>PO aktif</dt><dd>{profile?.active_po_count ?? "—"}</dd>
          </dl>
        </section>
        <button type="button" className="spwa-logout" onClick={onLogout} data-testid="sales-logout">
          Keluar
        </button>
      </main>
    </>
  );
}
