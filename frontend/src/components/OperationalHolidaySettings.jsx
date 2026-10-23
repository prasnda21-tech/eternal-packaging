import { useEffect, useState } from "react";
import api from "../lib/api";
import { newActionId } from "../lib/workflowEntries";
import "./OperationalHolidaySettings.css";

function formatHoliday(iso) {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  if (!y || !m || !d) return iso || "";
  return new Date(y, m - 1, d).toLocaleDateString("id-ID", {
    weekday: "short", day: "2-digit", month: "short", year: "numeric",
  });
}

export function OperationalHolidayReadonly() {
  return (
    <section className="ops-holiday" data-testid="operational-holiday-readonly">
      <header className="ops-holiday-head">
        <h2>Kalender Libur Operasional</h2>
        <p>Anda tidak memiliki izin untuk mengubah kalender libur.</p>
      </header>
    </section>
  );
}

export default function OperationalHolidaySettings({ canManage = true }) {
  const [rows, setRows] = useState([]);
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);

  const load = async () => {
    const body = await api.operationalHolidays();
    setRows(body.holidays || []);
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    load()
      .catch(() => { if (alive) setErr("Kalender libur tidak dapat dimuat."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const add = async () => {
    setErr("");
    setBusy(true);
    try {
      await api.addOperationalHoliday({ action_id: newActionId(), date, note });
      setDate("");
      setNote("");
      await load();
    } catch (e) {
      const detail = e.response?.data?.detail;
      setErr(typeof detail === "string" ? detail : (detail?.detail || "Gagal menambah libur"));
    } finally {
      setBusy(false);
    }
  };

  const saveNote = async (row) => {
    setBusy(true);
    setErr("");
    try {
      await api.updateOperationalHoliday(row.date, { action_id: newActionId(), note: draftNote });
      setEditing("");
      await load();
    } catch (e) {
      setErr("Gagal mengubah keterangan libur");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (row) => {
    setBusy(true);
    setErr("");
    try {
      await api.deleteOperationalHoliday(row.date, { action_id: newActionId(), note: row.note || "" });
      setPendingDelete(null);
      await load();
    } catch (e) {
      setErr("Gagal menghapus libur");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ops-holiday" data-testid="operational-holiday-panel">
      <header className="ops-holiday-head">
        <h2>Kalender Libur Operasional</h2>
        <p>Senin–Sabtu hari kerja. Minggu selalu libur. Tanggal di bawah ini libur tambahan dan tidak memakai kalender publik.</p>
      </header>

      {canManage ? (
        <div className="ops-holiday-card">
          <div className="ops-holiday-form">
            <label>
              Tanggal
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="holiday-date" />
            </label>
            <label>
              Keterangan
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Keterangan libur" data-testid="holiday-note" />
            </label>
            <button type="button" className="ops-holiday-add" disabled={busy || !date} onClick={add} data-testid="holiday-add">Tambah Libur</button>
          </div>
        </div>
      ) : null}

      {loading ? <p className="ops-holiday-feedback" data-testid="holiday-loading">Memuat kalender libur…</p> : null}
      {err ? <p className="ops-holiday-feedback ops-holiday-error" data-testid="holiday-error">{err}</p> : null}

      <div className="ops-holiday-card">
        <ul data-testid="holiday-list">
          {!loading && !rows.length ? <li className="ops-holiday-empty" data-testid="holiday-empty">Belum ada libur operasional.</li> : null}
          {rows.map((row) => (
            <li key={row.date} data-testid={`holiday-row-${row.date}`}>
              <div>
                <strong>{formatHoliday(row.date)}</strong>
                <span>{row.note || "Tanpa keterangan"}</span>
              </div>
              {canManage && editing === row.date ? (
                <div className="ops-holiday-edit">
                  <input
                    value={draftNote}
                    aria-label={`Keterangan ${row.date}`}
                    data-testid={`holiday-note-${row.date}`}
                    onChange={(e) => setDraftNote(e.target.value)}
                  />
                  <button type="button" disabled={busy} onClick={() => saveNote(row)} data-testid={`holiday-save-${row.date}`}>Simpan</button>
                  <button type="button" disabled={busy} onClick={() => setEditing("")}>Batal</button>
                </div>
              ) : null}
              {canManage && editing !== row.date ? (
                <div className="ops-holiday-actions">
                  <button type="button" disabled={busy} onClick={() => { setEditing(row.date); setDraftNote(row.note || ""); setPendingDelete(null); }} data-testid={`holiday-edit-${row.date}`}>Ubah</button>
                  <button type="button" disabled={busy} onClick={() => setPendingDelete(row.date)} data-testid={`holiday-delete-${row.date}`}>Hapus</button>
                </div>
              ) : null}
              {canManage && pendingDelete === row.date ? (
                <div className="ops-holiday-confirm" data-testid={`holiday-confirm-${row.date}`}>
                  <span>Hapus libur {formatHoliday(row.date)}?</span>
                  <button type="button" disabled={busy} onClick={() => remove(row)} data-testid="holiday-delete-confirm">Hapus</button>
                  <button type="button" disabled={busy} onClick={() => setPendingDelete(null)} data-testid="holiday-delete-cancel">Batal</button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
