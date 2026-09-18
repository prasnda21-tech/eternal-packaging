import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Camera, MapPin, Plus, Search, X } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesDate } from "../lib/salesPwa";

function monthCells(month) {
  const [year, rawMonth] = String(month || "").split("-").map(Number);
  if (!year || !rawMonth) return [];
  const first = new Date(year, rawMonth - 1, 1);
  const total = new Date(year, rawMonth, 0).getDate();
  const mondayOffset = (first.getDay() + 6) % 7;
  const cells = Array.from({ length: mondayOffset }, () => null);
  for (let day = 1; day <= total; day += 1) cells.push(`${year}-${String(rawMonth).padStart(2, "0")}-${String(day).padStart(2, "0")}`);
  return cells;
}

function geolocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(position.coords),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

export default function SalesPwaVisitsPage() {
  const [month, setMonth] = useState(currentSalesMonth());
  const [q, setQ] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [drawer, setDrawer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ result: "", next_action: "", next_follow_up_at: "", notes: "", file: null });

  const load = () => api.salesPwaVisits({ month, q }).then(setData).catch((err) => setError(err.response?.data?.detail || "Data visit gagal dimuat"));
  useEffect(() => {
    let cancelled = false;
    api.salesPwaVisits({ month, q })
        .then((result) => { if (!cancelled) { setData(result); setError(""); } })
        .catch((err) => { if (!cancelled) setError(err.response?.data?.detail || "Data visit gagal dimuat"); });
    return () => { cancelled = true; };
  }, [month, q]);

  const remindersByDate = useMemo(() => {
    const map = {};
    for (const reminder of data?.reminders || []) {
      (map[reminder.date] ||= []).push(reminder);
    }
    return map;
  }, [data]);

  const openVisit = (outlet) => {
    setForm({ result: "", next_action: "", next_follow_up_at: "", notes: "", file: null });
    setDrawer(outlet);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!drawer?.id || busy) return;
    const required = Boolean(data?.visit_config?.photo_required);
    if (required && !form.file) { setError("Foto dan lokasi wajib untuk visit."); return; }
    setBusy(true);
    setError("");
    try {
      const visitPhotoIds = [];
      if (form.file) {
        const coords = await geolocation();
        if (required && !coords) throw new Error("Lokasi visit tidak tersedia. Izinkan lokasi lalu coba lagi.");
        const body = new FormData();
        body.append("file", form.file);
        body.append("outlet_id", drawer.id);
        body.append("latitude", coords?.latitude ?? "");
        body.append("longitude", coords?.longitude ?? "");
        body.append("accuracy", coords?.accuracy ?? "");
        body.append("location_status", coords ? "OK" : "UNAVAILABLE");
        body.append("client_captured_at", new Date().toISOString());
        const uploaded = await api.uploadSalesPwaVisitPhoto(body);
        const photoId = uploaded?.photo?.id || uploaded?.id;
        if (photoId) visitPhotoIds.push(photoId);
      }
      await api.createSalesPwaVisit({
        outlet_id: drawer.id,
        result: form.result,
        next_action: form.next_action,
        next_follow_up_at: form.next_follow_up_at || null,
        notes: form.notes,
        visit_photo_ids: visitPhotoIds,
      });
      setDrawer(null);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Visit gagal disimpan");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <header className="spwa-top spwa-top-inline">
        <div><p className="spwa-kicker">SALES VISIT</p><h1>Visit Saya</h1></div>
        <input aria-label="Bulan visit" type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
      </header>
      <main className="spwa-main" data-testid="sales-pwa-visits">
        <label className="spwa-search"><Search size={14} /><input value={q} onChange={(event) => setQ(event.target.value)} placeholder="Cari outlet / PIC / alamat" /></label>
        {error && <div className="spwa-err">{typeof error === "string" ? error : "Terjadi kesalahan"}</div>}
        {data && (
          <>
            <section className="spwa-visit-summary">
              <span><strong>{data.counts?.owned_outlets || 0}</strong> Outlet</span>
              <span><strong>{data.counts?.visit_month || 0}</strong> Visit</span>
              <span className={data.counts?.overdue ? "is-alert" : ""}><strong>{data.counts?.overdue || 0}</strong> Terlambat</span>
            </section>
            <section className="spwa-calendar" aria-label="Kalender reminder visit">
              <div className="spwa-weekdays">{["S", "S", "R", "K", "J", "S", "M"].map((day, index) => <span key={index}>{day}</span>)}</div>
              <div className="spwa-calendar-grid">
                {monthCells(month).map((day, index) => {
                  const reminders = day ? remindersByDate[day] || [] : [];
                  return (
                    <button key={day || `blank-${index}`} type="button" disabled={!day} className={reminders.some((item) => item.overdue) ? "is-overdue" : reminders.length ? "has-event" : ""}>
                      {day && <><span>{Number(day.slice(-2))}</span>{reminders.length > 0 && <small>{reminders.length}</small>}</>}
                    </button>
                  );
                })}
              </div>
            </section>
            <section className="spwa-panel">
              <div className="spwa-section-head"><div><p className="spwa-kicker">OUTLET SAYA</p><h2>Jadwal & catat visit</h2></div></div>
              <div className="spwa-outlet-list">
                {(data.outlets || []).map((outlet) => {
                  const ownership = outlet.ownership || {};
                  return (
                    <article key={outlet.id}>
                      <div>
                        <strong>{outlet.name}</strong>
                        <span>{outlet.pic_name || outlet.address || "Tanpa PIC/alamat"}</span>
                        <small><CalendarDays size={13} /> {ownership.next_follow_up_at ? formatSalesDate(ownership.next_follow_up_at) : "Belum dijadwalkan"}</small>
                      </div>
                      <button type="button" onClick={() => openVisit(outlet)}><Plus size={15} /> Visit</button>
                    </article>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </main>
      {drawer && (
        <div className="spwa-drawer-backdrop" onClick={() => !busy && setDrawer(null)} role="presentation">
          <form className="spwa-drawer spwa-visit-drawer" onSubmit={submit} onClick={(event) => event.stopPropagation()}>
            <div className="spwa-drawer-head">
              <div><p className="spwa-kicker">CATAT VISIT</p><strong>{drawer.name}</strong></div>
              <button type="button" aria-label="Tutup" onClick={() => setDrawer(null)} disabled={busy}><X size={20} /></button>
            </div>
            <label>Hasil visit<input value={form.result} onChange={(event) => setForm({ ...form, result: event.target.value })} placeholder="Contoh: Minta sampel dan harga" required /></label>
            <label>Aksi berikutnya<input value={form.next_action} onChange={(event) => setForm({ ...form, next_action: event.target.value })} placeholder="Follow up penawaran" /></label>
            <label>Reminder follow-up<input type="datetime-local" value={form.next_follow_up_at} onChange={(event) => setForm({ ...form, next_follow_up_at: event.target.value })} required={Boolean(data?.visit_config?.next_follow_up_required)} /></label>
            <label>Catatan<textarea value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} rows={3} /></label>
            <label className="spwa-camera"><Camera size={17} /> Foto visit {data?.visit_config?.photo_required ? "(wajib)" : "(opsional)"}<input type="file" accept="image/*" capture="environment" onChange={(event) => setForm({ ...form, file: event.target.files?.[0] || null })} required={Boolean(data?.visit_config?.photo_required)} /></label>
            <p className="spwa-source-note compact"><MapPin size={14} /> Lokasi diambil saat foto dikirim dan diverifikasi backend.</p>
            <button className="spwa-primary" type="submit" disabled={busy}>{busy ? "Menyimpan..." : "Simpan Visit"}</button>
          </form>
        </div>
      )}
    </>
  );
}
