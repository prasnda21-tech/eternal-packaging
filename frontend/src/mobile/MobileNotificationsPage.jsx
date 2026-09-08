import MobileTopBar from "./MobileTopBar";

export default function MobileNotificationsPage() {
  return (
    <>
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Notifikasi" />
      <main className="mop-content" data-testid="mobile-notifications-page">
        <p className="mop-empty">Belum ada notifikasi.</p>
      </main>
    </>
  );
}
