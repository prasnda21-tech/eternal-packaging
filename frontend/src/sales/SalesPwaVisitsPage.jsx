import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Camera, MapPin, Plus, Search, X } from "lucide-react";
import api from "../lib/api";
import { currentSalesMonth, formatSalesWibInstant } from "../lib/salesPwa";

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

function filled(value) {
  return String(value || "").trim();
}

function visitWhen(value) {
  return formatSalesWibInstant(value);
}

function historyNewestFirst(rows) {
  return [...(rows || [])].sort((a, b) => String(b?.activity_at || "").localeCompare(String(a?.activity_at || "")));
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
  const [schedule, setSchedule] = useState(null);
  const [scheduleAt, setScheduleAt] = useState("");
  const [selectedDay, setSelectedDay] = useState("");
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

  const history = useMemo(() => historyNewestFirst(data?.visits), [data]);
  const agenda = useMemo(() => {
    const rows = [...(data?.follow_up_overdue || []), ...(data?.agenda_today || []), ...(data?.follow_up_upcoming || [])];
    if (!selectedDay) return rows;
    return rows.filter((item) => String(item.date || "").slice(0, 10) === selectedDay);
  }, [data, selectedDay]);

  const reminderByOutlet = useMemo(() => {
    const map = {};
    for (const item of data?.reminders || []) {
      if (item?.outlet_id) map[item.outlet_id] = item;
    }
    return map;
  }, [data]);

  const remindersByDate = useMemo(() => {
    const map = {};
    for (const reminder of data?.reminders || []) {
      (map[reminder.date] ||= []).push(reminder);
    }
    return map;
  }, [data]);

  const openVisit = (outlet) => {
    setForm({ result: "", next_action: "", next_follow_up_at: "", notes: "", file: null });
    setSchedule(null);
    setDrawer(outlet);
  };

  const openSchedule = (outlet) => {
    setScheduleAt("");
    setDrawer(null);
    setSchedule(outlet);
  };

  const saveSchedule = async (event) => {
    event.preventDefault();
    if (!schedule?.id || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.scheduleSalesPwaVisit({
        outlet_id: schedule.id,
        next_follow_up_at: scheduleAt,
      });
      setSchedule(null);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Jadwal gagal disimpan");
    } finally {
      setBusy(false);
    }
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
        <input aria-label="Bulan visit" type="month" value={month} onChange={(event) => { setMonth(event.target.value); setSelectedDay(""); }} />
      </header>
      <main className="spwa-main" data-testid="sales-pwa-visits">
        <label className="spwa-search"><Search size={14} /><input value={q} onChange={(event) => setQ(event.target.value)} placeholder="Cari outlet / PIC / alamat" /></label>
        {error && <div className="spwa-err">{typeof error === "string" ? error : "Terjadi kesalahan"}</div>}
        {!data && !error && <p className="spwa-muted">Memuat visit...</p>}
        {data && (
          <>
            <section className="spwa-panel" data-testid="sales-visit-agenda">
              <div className="spwa-section-head"><div><p className="spwa-kicker">AGENDA</p><h2>Yang harus dikerjakan</h2></div></div>
              <section className="spwa-calendar" aria-label="Kalender reminder visit">
                <div className="spwa-weekdays">{["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"].map((day) => <span key={day}>{day}</span>)}</div>
                <div className="spwa-calendar-grid">
                  {monthCells(month).map((day, index) => {
                    const reminders = day ? remindersByDate[day] || [] : [];
                    const selected = Boolean(day) && selectedDay === day;
                    return (
                      <button
                        key={day || `blank-${index}`}
                        type="button"
                        disabled={!day}
                        aria-pressed={selected}
                        onClick={() => day && setSelectedDay((current) => (current === day ? "" : day))}
                        className={[
                          reminders.some((item) => item.overdue) ? "is-overdue" : "",
                          reminders.length ? "has-event" : "",
                          selected ? "is-selected" : "",
                        ].filter(Boolean).join(" ")}
                      >
                        {day && <><span>{Number(day.slice(-2))}</span>{reminders.length > 0 && <small>{reminders.length}</small>}</>}
                      </button>
                    );
                  })}
                </div>
              </section>
              {agenda.length === 0 && <p className="spwa-empty compact">{selectedDay ? "Tidak ada agenda pada tanggal ini." : "Belum ada jadwal."}</p>}
              <div className="spwa-visit-history">
                {agenda.map((item) => (
                  <article key={item.id} data-testid="sales-visit-agenda-row">
                    <strong>{item.outlet || "Outlet"}</strong>
                    <span>{item.kind === "schedule" ? "Jadwal" : "Follow up"}{item.overdue ? " · Terlambat" : ""} · {visitWhen(item.at || item.date)}</span>
                    {filled(item.address) ? <small>{item.address}</small> : null}
                    {filled(item.next_action) ? <small>{item.next_action}</small> : null}
                  </article>
                ))}
              </div>
            </section>
            <section className="spwa-panel">
              <div className="spwa-section-head"><div><p className="spwa-kicker">OUTLET SAYA</p><h2>Jadwal & catat visit</h2></div></div>
              <div className="spwa-outlet-list">
                {(data.outlets || []).map((outlet) => {
                  const intent = reminderByOutlet[outlet.id];
                  const intentLabel = intent
                    ? `${intent.kind === "schedule" ? "Jadwal" : "Follow up"} · ${visitWhen(intent.at || intent.date)}`
                    : "Belum dijadwalkan";
                  return (
                    <article key={outlet.id}>
                      <div>
                        <strong>{outlet.name}</strong>
                        <span>{outlet.pic_name || outlet.address || "Tanpa PIC/alamat"}</span>
                        <small><CalendarDays size={13} /> {intentLabel}</small>
                      </div>
                      <div className="spwa-visit-actions">
                        <button type="button" onClick={() => openSchedule(outlet)}>Jadwalkan</button>
                        <button type="button" onClick={() => openVisit(outlet)}><Plus size={15} /> Visit</button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
            <section className="spwa-panel" data-testid="sales-visit-history">
              <div className="spwa-section-head"><div><p className="spwa-kicker">RIWAYAT</p><h2>Riwayat Visit</h2></div></div>
              {history.length === 0 && <p className="spwa-empty compact" data-testid="sales-visit-history-empty">Belum ada riwayat visit.</p>}
              <div className="spwa-visit-history">
                {history.map((visit) => {
                  const when = visitWhen(visit.activity_at);
                  const result = filled(visit.result);
                  const followUp = filled(visit.next_follow_up_at);
                  const nextAction = filled(visit.next_action);
                  const notes = filled(visit.notes);
                  return (
                    <article key={visit.id || `${visit.outlet}-${visit.activity_at}`} data-testid="sales-visit-history-row">
                      <strong>{visit.outlet || "Outlet"}</strong>
                      {when ? <span>{when}</span> : null}
                      {result ? <span>Hasil: {result}</span> : null}
                      {followUp ? <small>Follow-up: {visitWhen(followUp)}</small> : null}
                      {nextAction ? <small>Next action: {nextAction}</small> : null}
                      {notes ? <small>{notes}</small> : null}
                    </article>
                  );
                })}
              </div>
            </section>
            <section className="spwa-visit-summary" aria-label="Ringkasan visit">
              <span><strong>{data.counts?.owned_outlets || 0}</strong> Outlet</span>
              <span><strong>{data.counts?.visit_month || 0}</strong> Visit bulan ini</span>
              <span className={data.counts?.overdue ? "is-alert" : ""}><strong>{data.counts?.overdue || 0}</strong> Terlambat</span>
            </section>
          </>
        )}
      </main>
      {schedule && (
        <div className="spwa-drawer-backdrop" onClick={() => !busy && setSchedule(null)} role="presentation">
          <form className="spwa-drawer spwa-visit-drawer" data-testid="sales-visit-schedule" onSubmit={saveSchedule} onClick={(event) => event.stopPropagation()}>
            <div className="spwa-drawer-head">
              <div><p className="spwa-kicker">JADWALKAN VISIT</p><strong>{schedule.name}</strong></div>
              <button type="button" aria-label="Tutup jadwal" onClick={() => setSchedule(null)} disabled={busy}><X size={20} /></button>
            </div>
            <label>Tanggal dan waktu<input type="datetime-local" value={scheduleAt} onChange={(event) => setScheduleAt(event.target.value)} required /></label>
            <p className="spwa-source-note compact">Jadwal belum menjadi visit. Hasil dicatat saat kunjungan dilakukan.</p>
            <button className="spwa-primary" type="submit" disabled={busy}>{busy ? "Menyimpan..." : "Simpan Jadwal"}</button>
          </form>
        </div>
      )}
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
