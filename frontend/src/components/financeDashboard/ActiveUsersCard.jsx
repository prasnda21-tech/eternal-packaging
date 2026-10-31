import { useEffect, useState } from "react";
import api from "../../lib/api";

export function activityLabel(value, now) {
  const date = Date.parse(value);
  if (!Number.isFinite(date)) return "Belum tercatat";
  const minutes = Math.max(0, Math.floor((now - date) / 60000));
  if (minutes === 0) return "Baru saja";
  if (minutes < 60) return `${minutes} menit lalu`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} jam lalu`;
  return `${Math.floor(minutes / 1440)} hari lalu`;
}

export function fullActivity(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Belum tercatat";
  return new Intl.DateTimeFormat("id-ID", {
    timeZone: "Asia/Jakarta", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value)) + " WIB";
}

export default function ActiveUsersCard() {
  const [page, setPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    let busy = false;
    setSnapshot(null);
    setError("");
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const body = await api.activeUsers({ page, page_size: 50 });
        if (live) {
          setSnapshot({ ...body, received: performance.now() });
          setError("");
        }
      } catch (failure) {
        if (live) {
          setSnapshot(null);
          setError(failure?.response?.status === 403 ? "Tidak ada izin" : "Gagal memuat status pengguna");
        }
      } finally { busy = false; }
    };
    load();
    const timer = setInterval(load, 30000);
    const ticker = setInterval(() => setTick(value => value + 1), 5000);
    return () => { live = false; clearInterval(timer); clearInterval(ticker); };
  }, [page, attempt]);
  if (error) return <div role="status"><p>{error}</p><button type="button" className="kb-btn" onClick={() => setAttempt(value => value + 1)}>Coba lagi</button></div>;
  if (!snapshot) return <p role="status">Memuat pengguna…</p>;
  const now = Date.parse(snapshot.server_time) + performance.now() - snapshot.received;
  const pages = Math.max(1, Math.ceil(snapshot.total / snapshot.page_size));
  return <div className="kb-presence" data-refresh={tick}>
    <table>
      <thead><tr><th>Pengguna</th><th>Status</th><th>Terakhir aktif</th></tr></thead>
      <tbody>{snapshot.rows.map(row => {
        const age = now - Date.parse(row.last_seen_at);
        const online = Number.isFinite(age) && age >= 0 && age < snapshot.online_timeout_seconds * 1000;
        return <tr key={row.id}>
          <td>{row.name}</td>
          <td><span className={`kb-presence-status${online ? " is-online" : ""}`}>{online ? "● Online" : "○ Offline"}</span></td>
          <td><time dateTime={row.last_active_at || undefined} title={fullActivity(row.last_active_at)}>{activityLabel(row.last_active_at, now)}</time><small>{fullActivity(row.last_active_at)}</small></td>
        </tr>;
      })}</tbody>
    </table>
    {!snapshot.rows.length ? <p>Belum ada pengguna.</p> : null}
    <div className="kb-presence-pages">
      <button type="button" className="kb-btn" disabled={page === 1} onClick={() => setPage(value => value - 1)}>Sebelumnya</button>
      <span>{page} / {pages} · {snapshot.total} pengguna</span>
      <button type="button" className="kb-btn" disabled={page >= pages} onClick={() => setPage(value => value + 1)}>Berikutnya</button>
    </div>
  </div>;
}
