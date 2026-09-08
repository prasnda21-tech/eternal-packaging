import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import { permittedMobileWorkspaces, taskSummaryFromList } from "../lib/mobileWorkspaces";
import MobileTopBar from "./MobileTopBar";
import MobileTaskCard from "./components/MobileTaskCard";

export default function MobileTasksPage() {
  const { user, hasPerm } = useAuth();
  const workspaces = permittedMobileWorkspaces(hasPerm);
  const [wsId, setWsId] = useState(workspaces[0]?.id || "");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const selected = workspaces.find((w) => w.id === wsId) || workspaces[0] || null;

  useEffect(() => {
    if (!workspaces.length) return;
    if (!workspaces.some((w) => w.id === wsId)) setWsId(workspaces[0].id);
  }, [workspaces, wsId]);

  const load = async (division) => {
    if (!division) return;
    setBusy(true);
    setErr("");
    try {
      const d = await api.divisionTasks(division, "mine");
      setData(d);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat tugas.");
      setData({ tasks: [] });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (selected?.division) load(selected.division);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.division]);

  const tasks = data?.tasks || [];
  const summary = taskSummaryFromList(tasks);
  const roleLabel = ROLES[user?.role] || user?.role || "";

  return (
    <>
      <MobileTopBar
        kicker="ETERNAL PACKAGING"
        title="Tugas"
        subtitle={`${user?.name || user?.email || ""} · ${roleLabel}`.trim()}
      />
      <main className="mop-content" data-testid="mobile-tasks-page">
        <div className="mop-summary" data-testid="mobile-task-summary">
          <div className="mop-summary-cell"><span>Tugas Hari Ini</span><strong>{summary.hariIni}</strong></div>
          <div className="mop-summary-cell"><span>Aktif</span><strong>{summary.aktif}</strong></div>
          <div className="mop-summary-cell"><span>Proses</span><strong>{summary.proses}</strong></div>
          <div className="mop-summary-cell"><span>Menunggu</span><strong>{summary.menunggu}</strong></div>
        </div>
        {workspaces.length > 0 && (
          <div className="mop-ws-tabs" data-testid="mobile-workspace-tabs">
            {workspaces.map((ws) => (
              <button
                key={ws.id}
                type="button"
                className={`mop-ws-tab${selected?.id === ws.id ? " is-active" : ""}`}
                onClick={() => setWsId(ws.id)}
              >
                {ws.label}
              </button>
            ))}
          </div>
        )}
        {busy && !tasks.length && <p className="mop-empty">Memuat tugas...</p>}
        {err && <div className="mop-error">{err}</div>}
        {!workspaces.length && <p className="mop-empty">Tidak ada workspace operasional untuk akun ini.</p>}
        {selected && tasks.map((task) => (
          <MobileTaskCard key={task.task_id || task.order_id} task={task} workspaceId={selected.id} />
        ))}
        {selected && !busy && !tasks.length && !err && <p className="mop-empty">Tidak ada tugas.</p>}
      </main>
    </>
  );
}
